const tabs = document.querySelectorAll(".tab");
const pages = document.querySelectorAll(".page");
const alertBox = document.querySelector("#alert");
const state = {
  datasets: [],
  jobs: [],
  activeJobId: "",
  selectedPair: null,
  verification: {},
  resultsOffset: 0,
  resultsLimit: 100,
  faultDatasetId: "",
  faultJobId: "",
  demoJobIds: new Set(),
  lastSystem: null,
};

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[character]);
}

function showError(error) {
  alertBox.textContent = error instanceof Error ? error.message : String(error);
  alertBox.hidden = false;
}

function clearError() {
  alertBox.hidden = true;
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  let payload;
  try {
    payload = await response.json();
  } catch {
    payload = {};
  }
  if (!response.ok) {
    const detail = typeof payload.detail === "string"
      ? payload.detail
      : `Request failed with HTTP ${response.status}`;
    throw new Error(detail);
  }
  return payload;
}

function postJson(path, payload) {
  return api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

function wait(milliseconds) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

function formatDuration(seconds) {
  if (!Number.isFinite(Number(seconds))) return "—";
  const value = Math.max(0, Number(seconds));
  return value < 60
    ? `${value.toFixed(1)} s`
    : `${Math.floor(value / 60)}m ${(value % 60).toFixed(0)}s`;
}

function formatTime(timestamp) {
  if (!timestamp) return "—";
  return new Date(Number(timestamp) * 1000).toLocaleTimeString();
}

function setBusy(button, busy, busyText = "Working…") {
  if (!button.dataset.idleText) button.dataset.idleText = button.textContent;
  button.dataset.busy = String(busy);
  button.disabled = busy;
  button.textContent = busy ? busyText : button.dataset.idleText;
}

function showPage(name) {
  tabs.forEach((tab) => {
    const active = tab.dataset.tab === name;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
  });
  pages.forEach((page) => page.classList.toggle("active", page.id === `tab-${name}`));
  clearError();
  if (name === "system") refreshSystem();
  if (name === "demo") refreshSystem();
  if (name === "experiments") refreshExperiments();
  if (name === "results") refreshResults();
  if (name === "timeline") refreshTimeline();
}

tabs.forEach((tab) => tab.addEventListener("click", () => showPage(tab.dataset.tab)));

async function refreshApiHealth() {
  const pill = document.querySelector("#api-health");
  try {
    const health = await api("/api/health");
    pill.textContent = health.status === "healthy" ? "API online" : health.status;
    pill.className = `health-pill ${health.status === "healthy" ? "good" : "bad"}`;
  } catch {
    pill.textContent = "API unavailable";
    pill.className = "health-pill bad";
  }
}

function renderDatasetList() {
  const container = document.querySelector("#dataset-list");
  const select = document.querySelector("#job-dataset");
  const demoSelect = document.querySelector("#demo-dataset");
  const previous = select.value || state.activeDatasetId || "";
  if (!state.datasets.length) {
    container.innerHTML = '<p class="muted">No datasets yet. Generate or upload at least two text documents.</p>';
    select.innerHTML = '<option value="">No datasets available</option>';
    demoSelect.innerHTML = '<option value="">Generate a dataset first</option>';
    return;
  }
  container.innerHTML = state.datasets.map((dataset) => {
    const planted = dataset.ground_truth?.planted_pairs || [];
    const plantedLabel = dataset.synthetic
      ? `${planted.length} planted pair${planted.length === 1 ? "" : "s"}`
      : "No ground-truth manifest";
    const pairs = planted.map((pair) =>
      `${escapeHtml(pair.document_a)} ↔ ${escapeHtml(pair.document_b)} (${Math.round(Number(pair.nominal_overlap) * 100)}%)`
    ).join("<br>");
    return `<article class="dataset-row">
      <div><strong>${escapeHtml(dataset.dataset_id)}</strong>
        <small>${dataset.document_count} documents · ${dataset.pair_count.toLocaleString()} unique pairs · ${escapeHtml(dataset.source)}</small>
        <small>${escapeHtml(plantedLabel)}</small>
        ${pairs ? `<details><summary>View ground-truth manifest</summary><small>${pairs}</small></details>` : ""}
      </div>
      <button class="button secondary" data-select-dataset="${escapeHtml(dataset.dataset_id)}">Use dataset</button>
    </article>`;
  }).join("");
  select.innerHTML = state.datasets.map((dataset) =>
    `<option value="${escapeHtml(dataset.dataset_id)}">${escapeHtml(dataset.dataset_id)} · ${dataset.document_count} docs</option>`
  ).join("");
  demoSelect.innerHTML = state.datasets.map((dataset) =>
    `<option value="${escapeHtml(dataset.dataset_id)}">${escapeHtml(dataset.dataset_id)} · ${dataset.document_count} docs</option>`
  ).join("");
  if (state.datasets.some((dataset) => dataset.dataset_id === previous)) {
    select.value = previous;
  }
  demoSelect.value = state.datasets.some((dataset) => dataset.dataset_id === state.activeDatasetId)
    ? state.activeDatasetId
    : state.datasets[0].dataset_id;
  if (!state.activeDatasetId) state.activeDatasetId = demoSelect.value;
  container.querySelectorAll("[data-select-dataset]").forEach((button) => {
    button.addEventListener("click", () => {
      select.value = button.dataset.selectDataset;
      state.activeDatasetId = select.value;
      showPage("run");
    });
  });
}

async function refreshDatasets() {
  const payload = await api("/api/datasets");
  state.datasets = payload.datasets;
  renderDatasetList();
}

document.querySelector("#refresh-datasets").addEventListener("click", async () => {
  try {
    await refreshDatasets();
    clearError();
  } catch (error) {
    showError(error);
  }
});

document.querySelector("#generate-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector("button[type=submit]");
  const values = new FormData(form);
  const overlaps = String(values.get("overlaps")).split(",").map((value) => Number(value.trim()));
  setBusy(button, true);
  try {
    const dataset = await api("/api/datasets/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: values.get("name"),
        documents: Number(values.get("documents")),
        seed: Number(values.get("seed")),
        planted_pairs: Number(values.get("planted_pairs")),
        overlaps,
      }),
    });
    await refreshDatasets();
    document.querySelector("#job-dataset").value = dataset.dataset_id;
    state.activeDatasetId = dataset.dataset_id;
    clearError();
    showPage("run");
  } catch (error) {
    showError(error);
  } finally {
    setBusy(button, false);
  }
});

document.querySelector("#upload-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector("button[type=submit]");
  const body = new FormData(form);
  setBusy(button, true, "Uploading…");
  try {
    const dataset = await api("/api/datasets/upload", { method: "POST", body });
    await refreshDatasets();
    document.querySelector("#job-dataset").value = dataset.dataset_id;
    state.activeDatasetId = dataset.dataset_id;
    clearError();
    showPage("run");
  } catch (error) {
    showError(error);
  } finally {
    setBusy(button, false);
  }
});

function fillJobSelect(select, selectedId) {
  const previous = selectedId || select.value;
  if (!state.jobs.length) {
    select.innerHTML = '<option value="">No jobs submitted</option>';
    return;
  }
  select.innerHTML = state.jobs.map((job) =>
    `<option value="${escapeHtml(job.job_id)}">${escapeHtml(job.job_id.slice(0, 8))} · ${escapeHtml(job.status)} · ${job.n_docs} docs</option>`
  ).join("");
  if (state.jobs.some((job) => job.job_id === previous)) select.value = previous;
}

function syncJobSelectors() {
  ["#results-job", "#timeline-job"].forEach((selector) => {
    fillJobSelect(document.querySelector(selector), state.activeJobId);
  });
}

function renderJobList() {
  const container = document.querySelector("#job-list");
  if (!state.jobs.length) {
    container.innerHTML = '<p class="muted">No jobs submitted yet.</p>';
    return;
  }
  container.innerHTML = state.jobs.map((job) => `
    <button class="job-row" data-job-id="${escapeHtml(job.job_id)}" type="button">
      <span><strong>${escapeHtml(job.job_id)}</strong><small>${job.n_docs} documents · ${job.tasks_done}/${job.total_tasks} tasks · ${escapeHtml(job.status)}</small></span>
      <span class="status-badge ${job.status === "completed" ? "good" : job.status.includes("fail") ? "bad" : "pending"}">${escapeHtml(job.status)}</span>
    </button>`).join("");
  container.querySelectorAll("[data-job-id]").forEach((button) => {
    button.addEventListener("click", () => selectJob(button.dataset.jobId));
  });
}

async function refreshJobs() {
  const payload = await api("/api/jobs");
  state.jobs = payload.jobs;
  if (!state.activeJobId && state.jobs.length) state.activeJobId = state.jobs[0].job_id;
  syncJobSelectors();
  renderJobList();
  if (state.activeJobId) await refreshJobStatus();
}

function selectJob(jobId) {
  state.activeJobId = jobId;
  syncJobSelectors();
  refreshJobStatus().catch(showError);
  if (document.querySelector("#tab-results").classList.contains("active")) refreshResults().catch(showError);
  if (document.querySelector("#tab-timeline").classList.contains("active")) refreshTimeline().catch(showError);
}

function renderProgress(job) {
  const container = document.querySelector("#job-progress");
  const progress = job.total_tasks ? (job.tasks_done / job.total_tasks) * 100 : 0;
  const integrity = job.integrity || {};
  const workers = Object.entries(job.workers || {});
  container.innerHTML = `
    <div class="progress-head">
      <span class="status-badge ${job.status === "completed" ? "good" : job.status.includes("fail") ? "bad" : "pending"}">${escapeHtml(job.status)}</span>
      <code>${escapeHtml(job.job_id)}</code>
    </div>
    <div class="progress-track" role="progressbar" aria-valuenow="${Math.min(100, progress).toFixed(1)}" aria-valuemin="0" aria-valuemax="100"><span style="width:${Math.min(100, progress)}%"></span></div>
    <div class="metric-grid">
      <div class="metric"><small>Tasks completed</small><strong>${job.tasks_done} / ${job.total_tasks}</strong></div>
      <div class="metric"><small>Pairs compared</small><strong>${job.pairs_compared.toLocaleString()} / ${job.expected_pairs.toLocaleString()}</strong></div>
      <div class="metric"><small>Throughput</small><strong>${Number(job.pairs_per_second || 0).toFixed(1)} pairs/s</strong></div>
      <div class="metric"><small>Elapsed</small><strong>${formatDuration(job.elapsed_seconds)}</strong></div>
    </div>
    <p>Completed tasks: ${job.completed_tasks} · Reported in progress: ${job.in_progress_tasks} · Failed tasks: ${job.failed_tasks} · Reported retries: ${job.reported_retry_events} · Similar pairs saved: ${job.results_saved}</p>
    <div class="integrity-row">
      <span class="status-badge ${integrity.unique_task_ids ? "good" : "pending"}">Task IDs ${integrity.unique_task_ids ? "verified" : "pending"} (${integrity.unique_tasks_received}/${integrity.expected_tasks})</span>
      <span class="status-badge ${integrity.pair_count ? "good" : "pending"}">Pair count ${integrity.pair_count ? "verified" : "pending"} (${integrity.pairs_compared}/${integrity.expected_pairs})</span>
    </div>
    <h3>Worker contribution</h3>
    ${workers.length ? workers.map(([worker, counts]) => {
      const total = counts.tasks_processed + counts.tasks_failed;
      const width = job.tasks_done ? Math.max(2, (total / job.tasks_done) * 100) : 0;
      return `<div class="worker-contribution"><span>${escapeHtml(worker)} · ${counts.tasks_processed} completed, ${counts.tasks_failed} failed</span><div class="progress-track"><span style="width:${width}%"></span></div></div>`;
    }).join("") : '<p class="muted">Workers have not reported a result yet.</p>'}`;
  document.querySelector("#active-job-label").textContent =
    `${job.n_docs} documents · block size ${job.block_size} · threshold ${Number(job.threshold).toFixed(2)}`;
}

async function refreshJobStatus() {
  if (!state.activeJobId) return;
  try {
    const job = await api(`/api/jobs/${encodeURIComponent(state.activeJobId)}`);
    renderProgress(job);
    state.jobs = state.jobs.map((item) => item.job_id === job.job_id ? { ...item, ...job } : item);
    renderJobList();
  } catch (error) {
    if (error.message !== "Job not found.") throw error;
    document.querySelector("#job-progress").textContent = "Job not found.";
  }
}

document.querySelector("#job-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector("button[type=submit]");
  const values = new FormData(form);
  if (!values.get("dataset_id")) {
    showError(new Error("Select or create a dataset before starting a job."));
    return;
  }
  setBusy(button, true, "Submitting…");
  try {
    const job = await api("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        dataset_id: values.get("dataset_id"),
        threshold: Number(values.get("threshold")),
        block_size: Number(values.get("block_size")),
        inject_poison: form.elements.inject_poison.checked,
      }),
    });
    state.activeJobId = job.job_id;
    await refreshJobs();
    clearError();
  } catch (error) {
    showError(error);
  } finally {
    setBusy(button, false);
  }
});

document.querySelector("#refresh-job").addEventListener("click", () => {
  refreshJobStatus().catch(showError);
});

function plantedPairSet(dataset) {
  return new Set((dataset?.ground_truth?.planted_pairs || []).map((pair) =>
    [pair.document_a, pair.document_b].sort().join("|")
  ));
}

async function refreshResults() {
  const jobId = document.querySelector("#results-job").value || state.activeJobId;
  const body = document.querySelector("#results-body");
  if (!jobId) {
    body.innerHTML = '<tr><td colspan="5" class="muted">Select a completed or running job.</td></tr>';
    return;
  }
  const threshold = Number(document.querySelector("#minimum-score").value || 0);
  const [matches, job] = await Promise.all([
    api(`/api/jobs/${encodeURIComponent(jobId)}/results?minimum_score=${encodeURIComponent(threshold)}&limit=${state.resultsLimit}&offset=${state.resultsOffset}`),
    api(`/api/jobs/${encodeURIComponent(jobId)}`),
  ]);
  const dataset = state.datasets.find((item) => item.dataset_id === job.dataset_id);
  const planted = plantedPairSet(dataset);
  const verified = matches.verification;
  if (verified) state.verification[jobId] = verified;
  if (!matches.matches.length) {
    body.innerHTML = `<tr><td colspan="5" class="muted">No saved matches at this score threshold. Job status: ${escapeHtml(job.status)}.</td></tr>`;
  } else {
    body.innerHTML = matches.matches.map(([first, second, score], index) => {
      const key = [first, second].sort().join("|");
      const isPlanted = planted.has(key);
      const baselineText = verified?.passed ? "Matches baseline" : "—";
      const truthText = isPlanted ? "Matches ground truth" : (dataset?.synthetic ? "Not planted" : "Not applicable");
      return `<tr data-pair="${escapeHtml(first)}|${escapeHtml(second)}">
        <td>${matches.offset + index + 1}</td><td>${escapeHtml(first)}</td><td>${escapeHtml(second)}</td>
        <td class="score">${(Number(score) * 100).toFixed(2)}%</td>
        <td>${escapeHtml(truthText)}${verified?.passed ? `<br>${escapeHtml(baselineText)}` : ""}</td>
      </tr>`;
    }).join("");
    body.querySelectorAll("[data-pair]").forEach((row) => {
      row.addEventListener("click", () => {
        const [first, second] = row.dataset.pair.split("|");
        state.selectedPair = { jobId, datasetId: job.dataset_id, first, second };
        renderComparison(state.selectedPair).catch(showError);
      });
    });
  }
  const pageStart = matches.filtered_count ? matches.offset + 1 : 0;
  const pageEnd = matches.offset + matches.matches.length;
  document.querySelector("#results-page-label").textContent =
    `Showing ${pageStart}–${pageEnd} of ${matches.filtered_count} filtered matches`;
  document.querySelector("#previous-results").disabled = matches.offset === 0;
  document.querySelector("#next-results").disabled = !matches.has_more;
}

async function renderComparison(pair) {
  const container = document.querySelector("#comparison-detail");
  const [first, second] = await Promise.all([
    api(`/api/datasets/${encodeURIComponent(pair.datasetId)}/documents/${encodeURIComponent(pair.first)}`),
    api(`/api/datasets/${encodeURIComponent(pair.datasetId)}/documents/${encodeURIComponent(pair.second)}`),
  ]);
  const wordsA = first.text.split(/\s+/);
  const wordsB = second.text.split(/\s+/);
  const normalizedEntries = (words) => words.map((word, index) => ({
    word: word.toLowerCase().replace(/\p{P}/gu, ""),
    index,
  })).filter((entry) => entry.word);
  const shingles = (entries) => {
    const set = new Set();
    for (let index = 0; index <= entries.length - 5; index += 1) {
      set.add(entries.slice(index, index + 5).map((entry) => entry.word).join("\u001f"));
    }
    return set;
  };
  const entriesA = normalizedEntries(wordsA);
  const entriesB = normalizedEntries(wordsB);
  const shinglesB = shingles(entriesB);
  const shared = new Set([...shingles(entriesA)].filter((shingle) => shinglesB.has(shingle)));
  const highlight = (words, entries) => {
    const marked = new Set();
    for (let index = 0; index <= entries.length - 5; index += 1) {
      if (shared.has(entries.slice(index, index + 5).map((entry) => entry.word).join("\u001f"))) {
        for (let offset = 0; offset < 5; offset += 1) marked.add(entries[index + offset].index);
      }
    }
    return words.map((word, index) =>
      marked.has(index) ? `<mark>${escapeHtml(word)}</mark>` : escapeHtml(word)
    ).join(" ");
  };
  const documentCard = (title, text, words) =>
    `<article class="card"><h2>${escapeHtml(title)}</h2><p class="muted">${text.length.toLocaleString()} characters · shared five-word shingles highlighted</p><pre class="document-preview">${highlight(words, title === first.filename ? entriesA : entriesB)}</pre></article>`;
  container.innerHTML = `
    <article class="card wide"><div class="section-title"><h2>Pair review: ${escapeHtml(pair.first)} ↔ ${escapeHtml(pair.second)}</h2><button id="close-comparison" class="button quiet" type="button">Close</button></div><p>${shared.size.toLocaleString()} shared unique five-word shingles. Highlighting is a review aid, not a plagiarism verdict.</p></article>
    ${documentCard(first.filename, first.text, wordsA)}
    ${documentCard(second.filename, second.text, wordsB)}`;
  document.querySelector("#close-comparison").addEventListener("click", () => {
    state.selectedPair = null;
    container.replaceChildren();
  });
}

document.querySelector("#results-job").addEventListener("change", (event) => {
  state.activeJobId = event.target.value;
  state.resultsOffset = 0;
  state.selectedPair = null;
  document.querySelector("#comparison-detail").replaceChildren();
  refreshResults().catch(showError);
});
document.querySelector("#minimum-score").addEventListener("change", () => {
  state.resultsOffset = 0;
  refreshResults().catch(showError);
});
document.querySelector("#refresh-results").addEventListener("click", () => refreshResults().catch(showError));
document.querySelector("#previous-results").addEventListener("click", () => {
  state.resultsOffset = Math.max(0, state.resultsOffset - state.resultsLimit);
  refreshResults().catch(showError);
});
document.querySelector("#next-results").addEventListener("click", () => {
  state.resultsOffset += state.resultsLimit;
  refreshResults().catch(showError);
});
document.querySelector("#verify-job").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  const jobId = document.querySelector("#results-job").value || state.activeJobId;
  if (!jobId) {
    showError(new Error("Select a job to verify."));
    return;
  }
  setBusy(button, true, "Comparing…");
  try {
    const result = await api(`/api/jobs/${encodeURIComponent(jobId)}/verify`, { method: "POST" });
    state.verification[jobId] = result;
    const summary = document.querySelector("#verify-summary");
    summary.hidden = false;
    summary.innerHTML = `<strong>${result.passed ? "PASS" : "FAIL"} — baseline comparison</strong><br>
      Pair set: ${result.pair_set_match ? "exact match" : "different"} · Scores to 6 decimals: ${result.scores_match_to_6_decimals ? "match" : "different"}<br>
      Pair coverage: ${result.pairs_compared}/${result.expected_pairs} · Planted-pair recall: ${result.planted_pair_recall == null ? "not applicable" : `${result.planted_pairs_recalled}/${result.planted_pair_count} (${(result.planted_pair_recall * 100).toFixed(1)}%)`}`;
    await refreshResults();
    clearError();
  } catch (error) {
    showError(error);
  } finally {
    setBusy(button, false);
  }
});

function renderSystem(data, dns, apiHealth) {
  state.lastSystem = data;
  const summary = document.querySelector("#system-summary");
  const queue = data.queues?.tasks || {};
  const values = [
    ["System", data.status || "unavailable"],
    ["Active workers", `${data.active_workers ?? "—"} / ${data.task_consumers ?? "—"}`],
    ["Task queue ready", queue.ready ?? "—"],
    ["Task queue unacked", queue.unacknowledged ?? "—"],
  ];
  summary.innerHTML = values.map(([label, value]) =>
    `<div class="metric"><small>${escapeHtml(label)}</small><strong>${escapeHtml(value)}</strong></div>`
  ).join("");
  const rabbitStatus = Object.values(data.queues || {}).every((item) => item.status === "healthy")
    ? "Healthy"
    : "Unavailable";
  const workerStatus = data.task_consumers > 0
    ? `${data.active_workers} active · ${data.task_consumers} consumers`
    : "No task consumers";
  const services = [
    ["API", apiHealth ? "Healthy" : "Unavailable", apiHealth],
    ["RabbitMQ", rabbitStatus, rabbitStatus === "Healthy"],
    ["Aggregator RPC", data.aggregator?.status || "Unavailable", data.aggregator?.status === "healthy"],
    ["Worker pool", workerStatus, data.task_consumers > 0],
  ];
  document.querySelector("#service-status").innerHTML = services.map(([name, status, healthy]) =>
    `<div class="service-status-item"><strong>${escapeHtml(name)}</strong><span class="status-badge ${healthy ? "good" : "bad"}">${escapeHtml(status)}</span></div>`
  ).join("");
  document.querySelector("#queue-list").innerHTML = Object.values(data.queues || {}).map((item) =>
    `<div class="queue-row"><span><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.status)}</small></span>
      <span class="queue-count">${item.ready ?? "—"} ready · ${item.unacknowledged ?? "—"} unacked · ${item.consumers ?? 0} consumers</span></div>`
  ).join("") || '<p class="muted">Queue telemetry unavailable.</p>';
  const workers = data.workers || [];
  document.querySelector("#worker-list").innerHTML = workers.length
    ? workers.map((worker) => `<div class="worker-row"><span><strong>${escapeHtml(worker.worker_id)}</strong><small>${worker.healthy ? "Heartbeat healthy" : "Heartbeat stale"} · ${worker.current_task ? `working on ${escapeHtml(worker.current_task.slice(0, 8))}` : "idle"}</small></span><span>${worker.tasks_processed || 0} completed · ${worker.tasks_failed || 0} failed</span></div>`).join("")
    : '<p class="muted">No recent worker heartbeats. Scale workers up or check aggregator logs.</p>';
  document.querySelector("#dns-list").innerHTML = Object.entries(dns).map(([name, result]) =>
    `<div class="dataset-row"><span><strong>${escapeHtml(name)}</strong><small>${escapeHtml((result.addresses || []).join(", ") || result.detail || "not resolved")}</small></span></div>`
  ).join("");
  const kill = document.querySelector("#kill-worker");
  kill.hidden = !data.demo_controls;
  const activeJob = state.jobs.find((job) => job.job_id === state.activeJobId);
  const activeTask = workers.some((worker) => worker.healthy && worker.current_task);
  kill.disabled = !activeJob
    || activeJob.status !== "processing"
    || (data.task_consumers || 0) < 2
    || !activeTask
    || (queue.unacknowledged || 0) < 1;
  kill.title = kill.disabled
    ? "Requires a processing job, two consumers, a fresh active worker, and an unacknowledged task."
    : "Send an explicitly simulated kill control to one active worker.";
  updateDemoControls();
  updateQueueDemoStatus(data);
}

async function refreshSystem() {
  const names = ["worker", "aggregator", "rabbitmq", "api"];
  const [system, health, ...dnsResults] = await Promise.all([
    api("/api/system"),
    api("/api/health").catch(() => null),
    ...names.map((name) => api(`/api/system/dns?name=${name}`).catch((error) => ({ name, detail: error.message, addresses: [] }))),
  ]);
  renderSystem(system, Object.fromEntries(dnsResults.map((result) => [result.name, result])), health?.status === "healthy");
}

document.querySelector("#kill-worker").addEventListener("click", async (event) => {
  if (!state.activeJobId || !window.confirm("Send a SIMULATED kill control to one active worker? Its unacknowledged task should be redelivered.")) return;
  const button = event.currentTarget;
  setBusy(button, true, "Sending control…");
  try {
    const result = await api("/api/system/kill-random-worker", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: state.activeJobId }),
    });
    showError(new Error(`Kill control sent to ${result.target_worker}. Observe task redelivery in RabbitMQ and worker logs.`));
    await refreshSystem();
  } catch (error) {
    showError(error);
  } finally {
    setBusy(button, false);
  }
});

function demoDatasetId() {
  return document.querySelector("#demo-dataset").value;
}

function setDemoStatus(id, message) {
  document.querySelector(`#${id}`).textContent = message;
}

function updateDemoControls() {
  const data = state.lastSystem || {};
  const enabled = Boolean(data.demo_controls);
  const healthyWorkers = (data.workers || []).filter((worker) => worker.healthy);
  const faultJob = state.jobs.find((job) => job.job_id === state.faultJobId);
  document.querySelector("#demo-control-warning").hidden = enabled;
  const disabledUnlessIdle = (selector, shouldDisable) => {
    const button = document.querySelector(selector);
    button.disabled = button.dataset.busy === "true" || shouldDisable;
  };
  disabledUnlessIdle("#pause-workers", !enabled || healthyWorkers.length === 0);
  disabledUnlessIdle("#resume-workers", !enabled || healthyWorkers.length === 0);
  disabledUnlessIdle("#submit-queued-job", !demoDatasetId());
  disabledUnlessIdle("#start-poison-demo", !demoDatasetId());
  disabledUnlessIdle("#prepare-fault-dataset", !enabled);
  disabledUnlessIdle(
    "#start-fault-job",
    !enabled || !state.faultDatasetId || faultJob?.status === "processing",
  );
  disabledUnlessIdle("#kill-demo-worker", !enabled
    || !faultJob
    || faultJob.status !== "processing"
    || (data.task_consumers || 0) < 2);
  disabledUnlessIdle("#start-clock-demo", !enabled || !demoDatasetId());
  disabledUnlessIdle("#reset-clock-skew", !enabled);
  disabledUnlessIdle("#run-browser-benchmark", !enabled || !demoDatasetId());
  if (state.faultDatasetId) {
    const startFaultButton = document.querySelector("#start-fault-job");
    if (startFaultButton.dataset.busy !== "true") {
      startFaultButton.textContent =
        `Start failure-tolerance workload · ${state.faultDatasetId}`;
    }
  }
}

function updateQueueDemoStatus(data) {
  if (!document.querySelector("#tab-demo")) return;
  const queue = data.queues?.tasks || {};
  const workers = data.task_consumers ?? 0;
  const job = state.jobs.find((item) => item.job_id === state.queueDemoJobId);
  const parts = [
    `Queue: ${queue.ready ?? "—"} ready, ${queue.unacknowledged ?? "—"} unacked`,
    `${workers} task consumers`,
  ];
  if (job) parts.unshift(`Job ${job.job_id.slice(0, 8)}: ${job.tasks_done}/${job.total_tasks} tasks`);
  const status = document.querySelector("#queue-demo-status");
  if (state.queueDemoMessage) {
    status.textContent = `${state.queueDemoMessage} ${parts.join(" · ")}`;
  }
}

async function setActiveConsumers(count) {
  return postJson("/api/system/worker-consumers", { active_workers: count });
}

document.querySelector("#demo-dataset").addEventListener("change", (event) => {
  state.activeDatasetId = event.target.value;
  updateDemoControls();
});

document.querySelector("#pause-workers").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  setBusy(button, true, "Pausing…");
  try {
    const result = await setActiveConsumers(0);
    await waitForConsumerCount(0);
    state.queueDemoMessage = `Pause sent to ${result.available_workers} worker container(s).`;
    await refreshSystem();
    clearError();
  } catch (error) {
    showError(error);
  } finally {
    setBusy(button, false);
  }
});

document.querySelector("#submit-queued-job").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  const datasetId = demoDatasetId();
  if (!datasetId) return showError(new Error("Choose a dataset first."));
  setBusy(button, true, "Submitting…");
  try {
    const job = await postJson("/api/jobs", {
      dataset_id: datasetId,
      threshold: 0.1,
      block_size: 4,
    });
    state.activeJobId = job.job_id;
    state.queueDemoJobId = job.job_id;
    state.queueDemoMessage = `Published ${job.submitted_tasks} persistent tasks for job ${job.job_id.slice(0, 8)}.`;
    await refreshJobs();
    await refreshSystem();
    trackDemoJob(job.job_id, "queue-demo-status", "Queue demonstration").catch(showError);
    clearError();
  } catch (error) {
    showError(error);
  } finally {
    setBusy(button, false);
  }
});

document.querySelector("#resume-workers").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  setBusy(button, true, "Resuming…");
  try {
    const system = await api("/api/system");
    const count = (system.workers || []).filter((worker) => worker.healthy).length;
    const result = await setActiveConsumers(count);
    await waitForConsumerCount(result.requested);
    state.queueDemoMessage = `Resume sent to ${result.requested} healthy worker container(s).`;
    await refreshSystem();
    clearError();
  } catch (error) {
    showError(error);
  } finally {
    setBusy(button, false);
  }
});

async function startDemoJob({ datasetId, blockSize, injectPoison = false }) {
  const job = await postJson("/api/jobs", {
    dataset_id: datasetId,
    threshold: 0.1,
    block_size: blockSize,
    inject_poison: injectPoison,
  });
  state.activeJobId = job.job_id;
  await refreshJobs();
  return job;
}

async function trackDemoJob(jobId, statusElementId, label, onComplete) {
  if (state.demoJobIds.has(jobId)) return;
  state.demoJobIds.add(jobId);
  const deadline = Date.now() + 30 * 60 * 1000;
  try {
    while (Date.now() < deadline) {
      const job = await api(`/api/jobs/${encodeURIComponent(jobId)}`);
      state.jobs = state.jobs.map((item) => item.job_id === job.job_id ? { ...item, ...job } : item);
      setDemoStatus(
        statusElementId,
        `${label}: ${job.status} · ${job.tasks_done}/${job.total_tasks} tasks · ${job.pairs_compared.toLocaleString()}/${job.expected_pairs.toLocaleString()} pairs.`,
      );
      renderJobList();
      updateDemoControls();
      if (job.status === "completed" || job.status === "completed_with_failures") {
        const verification = await api(`/api/jobs/${encodeURIComponent(jobId)}/verify`, { method: "POST" });
        await refreshSystem();
        if (onComplete) await onComplete(job, verification);
        return;
      }
      await wait(1000);
    }
    throw new Error(`${label} did not finish within 30 minutes. Job ${jobId} remains available in Job history.`);
  } catch (error) {
    setDemoStatus(statusElementId, `${label} error: ${error.message}`);
    throw error;
  } finally {
    state.demoJobIds.delete(jobId);
  }
}

document.querySelector("#start-poison-demo").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  const failedBefore = state.lastSystem?.queues?.failed_tasks?.ready ?? 0;
  setBusy(button, true, "Submitting failure injection…");
  try {
    const job = await startDemoJob({
      datasetId: demoDatasetId(),
      blockSize: 4,
      injectPoison: true,
    });
    setDemoStatus("poison-demo-status", `Job ${job.job_id.slice(0, 8)} started with one invalid task.`);
    await trackDemoJob(job.job_id, "poison-demo-status", "Retry demo", async (status, verification) => {
      const system = await waitForQueueReady("failed_tasks", failedBefore + 1);
      const failedAfter = system.queues.failed_tasks?.ready ?? 0;
      setDemoStatus(
        "poison-demo-status",
        `${status.status} · ${status.reported_retry_events} retries · failed queue ${failedBefore} → ${failedAfter} · pair check ${status.integrity.pair_count ? "PASS" : "FAIL"} · baseline ${verification.passed ? "PASS" : "FAIL"}.`,
      );
    });
    await refreshSystem();
  } catch (error) {
    showError(error);
  } finally {
    setBusy(button, false);
  }
});

document.querySelector("#prepare-fault-dataset").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  setBusy(button, true, "Generating synthetic dataset…");
  try {
    let dataset = state.datasets.find((item) => item.document_count === 500);
    if (!dataset) {
      dataset = await postJson("/api/datasets/generate", {
        name: "worker-failure-demo",
        documents: 500,
        seed: 2026,
        planted_pairs: 5,
        overlaps: [0.3, 0.6, 0.9],
      });
    }
    state.faultDatasetId = dataset.dataset_id;
    await refreshDatasets();
    document.querySelector("#demo-dataset").value = dataset.dataset_id;
    updateDemoControls();
    setDemoStatus("fault-demo-status", `Ready: ${dataset.document_count} documents · ${dataset.pair_count.toLocaleString()} pairs.`);
  } catch (error) {
    showError(error);
  } finally {
    setBusy(button, false);
  }
});

document.querySelector("#start-fault-job").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  if (!state.faultDatasetId) return showError(new Error("Create the fault-tolerance dataset first."));
  setBusy(button, true, "Starting workload…");
  let workersPaused = false;
  let consumerCount = 0;
  try {
    const system = await api("/api/system");
    consumerCount = system.task_consumers;
    if (consumerCount < 2) {
      throw new Error("At least two workers must be running.");
    }
    state.faultRedeliveriesBefore = system.queues.tasks.redeliveries || 0;
    await setActiveConsumers(0);
    workersPaused = true;
    await waitForConsumerCount(0);
    setDemoStatus("fault-demo-status", "Queueing tasks…");
    const job = await startDemoJob({ datasetId: state.faultDatasetId, blockSize: 8 });
    state.faultJobId = job.job_id;
    setDemoStatus("fault-demo-status", `${job.total_tasks.toLocaleString()} tasks queued. Starting ${consumerCount} workers…`);
    await setActiveConsumers(consumerCount);
    await waitForConsumerCount(consumerCount);
    workersPaused = false;
    await refreshJobs();
    updateDemoControls();
    trackDemoJob(job.job_id, "fault-demo-status", "Worker test", async (status, verification) => {
      const latest = await waitForWorkerRecovery(
        state.faultRedeliveriesBefore || 0,
        2,
      );
      const redeliveries = latest.queues.tasks.redeliveries || 0;
      const recovered = redeliveries > (state.faultRedeliveriesBefore || 0);
      setDemoStatus(
        "fault-demo-status",
        `${status.status} · redelivery ${recovered ? "yes" : "no"} (${state.faultRedeliveriesBefore || 0} → ${redeliveries}) · task check ${status.integrity.unique_task_ids ? "PASS" : "FAIL"} · pair check ${status.integrity.pair_count ? "PASS" : "FAIL"} · baseline ${verification.passed ? "PASS" : "FAIL"}.`,
      );
    }).catch(showError);
  } catch (error) {
    let message = error.message;
    if (workersPaused) {
      try {
        await setActiveConsumers(consumerCount);
        await waitForConsumerCount(consumerCount);
      } catch (resumeError) {
        message += ` Worker recovery also failed: ${resumeError.message}`;
      }
    }
    setDemoStatus("fault-demo-status", message);
    showError(new Error(message));
  } finally {
    setBusy(button, false);
  }
});

document.querySelector("#kill-demo-worker").addEventListener("click", async (event) => {
  if (!state.faultJobId || !window.confirm("Stop one busy worker? Its unacknowledged task should be sent back to the queue.")) return;
  const button = event.currentTarget;
  setBusy(button, true, "Sending kill control…");
  try {
    const result = await postJson("/api/system/kill-random-worker", { job_id: state.faultJobId });
    setDemoStatus("fault-demo-status", `Stopping worker ${result.target_worker}…`);
    await refreshSystem();
  } catch (error) {
    setDemoStatus("fault-demo-status", error.message);
    showError(error);
  } finally {
    setBusy(button, false);
  }
});

document.querySelector("#start-clock-demo").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  const seconds = Number(document.querySelector("#demo-clock-skew").value);
  setBusy(button, true, "Applying simulated offset…");
  try {
    await postJson("/api/system/clock-skew", { seconds });
    await wait(1200);
    const job = await startDemoJob({ datasetId: demoDatasetId(), blockSize: 4 });
    setDemoStatus("clock-demo-status", `Job ${job.job_id.slice(0, 8)} started · worker time offset ${seconds}s.`);
    trackDemoJob(job.job_id, "clock-demo-status", "Timeline demo", async (status, verification) => {
      document.querySelector("#timeline-job").value = status.job_id;
      await refreshTimeline();
      setDemoStatus("clock-demo-status", `${status.status} · offset ${seconds}s · baseline ${verification.passed ? "PASS" : "FAIL"}.`);
      showPage("timeline");
    });
  } catch (error) {
    showError(error);
    setDemoStatus("clock-demo-status", error.message);
  } finally {
    setBusy(button, false);
  }
});

document.querySelector("#reset-clock-skew").addEventListener("click", async () => {
  try {
    await postJson("/api/system/clock-skew", { seconds: 0 });
    await wait(700);
    setDemoStatus("clock-demo-status", "Worker time offset reset.");
    clearError();
  } catch (error) {
    showError(error);
  }
});

async function waitForConsumerCount(count) {
  const deadline = Date.now() + 15000;
  let system;
  do {
    system = await api("/api/system");
    if (system.task_consumers === count) return;
    await wait(300);
  } while (Date.now() < deadline);
  throw new Error(`Worker control requested ${count} consumers, but RabbitMQ reports ${system.task_consumers}.`);
}

async function waitForQueueReady(queueName, minimum, timeout = 15000) {
  const deadline = Date.now() + timeout;
  let system;
  do {
    system = await api("/api/system");
    if ((system.queues[queueName]?.ready || 0) >= minimum) return system;
    await wait(500);
  } while (Date.now() < deadline);
  return system;
}

async function waitForWorkerRecovery(redeliveriesBefore, minimumConsumers, timeout = 20000) {
  const deadline = Date.now() + timeout;
  let system;
  do {
    system = await api("/api/system");
    const redeliveries = system.queues.tasks?.redeliveries || 0;
    if (redeliveries > redeliveriesBefore && system.task_consumers >= minimumConsumers) {
      return system;
    }
    await wait(500);
  } while (Date.now() < deadline);
  return system;
}

async function submitAndMeasure(datasetId, blockSize, statusElement, label) {
  const started = performance.now();
  const job = await startDemoJob({ datasetId, blockSize });
  const deadline = Date.now() + 30 * 60 * 1000;
  while (Date.now() < deadline) {
    const status = await api(`/api/jobs/${encodeURIComponent(job.job_id)}`);
    setDemoStatus(
      statusElement,
      `${label}: job ${job.job_id.slice(0, 8)} · ${status.status} · ${status.tasks_done}/${status.total_tasks} tasks · ${status.pairs_compared.toLocaleString()}/${status.expected_pairs.toLocaleString()} pairs.`,
    );
    if (status.status === "completed") {
      if (!status.integrity.unique_task_ids || !status.integrity.pair_count) {
        throw new Error(`Benchmark job ${job.job_id} did not pass both integrity checks.`);
      }
      return {
        job_id: job.job_id,
        seconds: (performance.now() - started) / 1000,
      };
    }
    if (status.status === "completed_with_failures") {
      throw new Error(`Benchmark job ${job.job_id} completed with failures.`);
    }
    await wait(500);
  }
  throw new Error(`Benchmark job ${job.job_id} exceeded the 30-minute timeout.`);
}

document.querySelector("#run-browser-benchmark").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  const datasetId = demoDatasetId();
  const repeats = Number(document.querySelector("#benchmark-repeats").value);
  if (!datasetId) return showError(new Error("Choose a dataset for the benchmark."));
  setBusy(button, true, "Benchmark running…");
  let originalConsumers = null;
  try {
    const [system, dataset] = await Promise.all([
      api("/api/system"),
      api(`/api/datasets/${encodeURIComponent(datasetId)}`),
    ]);
    const availableWorkers = (system.workers || []).filter((worker) => worker.healthy).length;
    if (availableWorkers < 1) throw new Error("No healthy worker containers are available.");
    originalConsumers = system.task_consumers;
    const runs = [];
    for (let workerCount = 1; workerCount <= availableWorkers; workerCount += 1) {
      setDemoStatus("browser-benchmark-status", `Configuring ${workerCount}/${availableWorkers} active worker(s)…`);
      await setActiveConsumers(workerCount);
      await waitForConsumerCount(workerCount);
      const warmup = await submitAndMeasure(datasetId, 8, "browser-benchmark-status", `${workerCount} worker warm-up`);
      const timings = [];
      const jobIds = [];
      for (let repeat = 1; repeat <= repeats; repeat += 1) {
        const measured = await submitAndMeasure(
          datasetId,
          8,
          "browser-benchmark-status",
          `${workerCount} workers · repeat ${repeat}/${repeats}`,
        );
        timings.push(measured.seconds);
        jobIds.push(measured.job_id);
      }
      const sorted = [...timings].sort((a, b) => a - b);
      const medianSeconds = sorted.length % 2
        ? sorted[Math.floor(sorted.length / 2)]
        : (sorted[sorted.length / 2 - 1] + sorted[sorted.length / 2]) / 2;
      runs.push({
        workers: workerCount,
        warmup_job_id: warmup.job_id,
        job_ids: jobIds,
        run_times_seconds: timings,
        median_seconds: medianSeconds,
        pairs_per_second: dataset.pair_count / medianSeconds,
        speedup: 0,
        efficiency: 0,
      });
      const baseline = runs[0].median_seconds;
      runs[workerCount - 1].speedup = baseline / medianSeconds;
      runs[workerCount - 1].efficiency = runs[workerCount - 1].speedup / workerCount;
    }
    const report = {
      benchmark_id: `browser-${Date.now()}`,
      created_at: new Date().toISOString(),
      dataset_id: datasetId,
      document_count: dataset.document_count,
      expected_pairs: dataset.pair_count,
      threshold: 0.1,
      block_size: 8,
      repeats,
      warmup_jobs: 1,
      host_logical_cpus: navigator.hardwareConcurrency || 1,
      configuration: {
        max_workers: availableWorkers,
        timing: "browser performance.now() from job submission to first observed completion; includes HTTP and polling overhead",
        worker_count_semantics: "active RabbitMQ task consumers selected from already-running healthy worker containers",
        browser_hardware_concurrency_hint: navigator.hardwareConcurrency || 1,
      },
      runs,
    };
    await postJson("/api/experiments/import", report);
    await refreshExperiments();
    setDemoStatus(
      "browser-benchmark-status",
      `Measured ${runs.length} active worker configuration(s), ${repeats} timed repeats plus warm-up each. Report saved; all jobs passed integrity checks. Open Experiments to compare results.`,
    );
    clearError();
  } catch (error) {
    showError(error);
    setDemoStatus("browser-benchmark-status", `Benchmark stopped: ${error.message}`);
  } finally {
    if (originalConsumers !== null) {
      try {
        await setActiveConsumers(originalConsumers);
      } catch (error) {
        showError(new Error(`Benchmark finished, but could not restore the original worker count: ${error.message}`));
      }
    }
    setBusy(button, false);
  }
});

document.querySelector("#open-experiments").addEventListener("click", () => showPage("experiments"));

function renderTimeline(events, element) {
  element.innerHTML = events.length
    ? events.map((event) => `<li><strong>${escapeHtml(event.event)}</strong> · ${escapeHtml(event.service)} · LC ${event.lc}
      <time>${new Date(Number(event.wall_time_reported) * 1000).toLocaleString()}${event.task_id ? ` · task ${escapeHtml(event.task_id.slice(0, 8))}` : ""}</time></li>`).join("")
    : "<li>No events recorded yet.</li>";
}

async function refreshTimeline() {
  const jobId = document.querySelector("#timeline-job").value || state.activeJobId;
  if (!jobId) return;
  const [wall, lamport] = await Promise.all([
    api(`/api/jobs/${encodeURIComponent(jobId)}/timeline?order=wall`),
    api(`/api/jobs/${encodeURIComponent(jobId)}/timeline?order=lamport`),
  ]);
  document.querySelector("#skew-notice").hidden = !wall.simulated_clock_skew;
  renderTimeline(wall.events, document.querySelector("#wall-timeline"));
  renderTimeline(lamport.events, document.querySelector("#lamport-timeline"));
}

document.querySelector("#timeline-job").addEventListener("change", refreshTimeline);
document.querySelector("#refresh-timeline").addEventListener("click", () => refreshTimeline().catch(showError));

function renderBenchmarkChart(runs) {
  const svg = document.querySelector("#benchmark-chart");
  if (!runs.length) {
    svg.innerHTML = '<text x="360" y="150" text-anchor="middle" fill="#778397">No measured benchmark data to chart.</text>';
    return;
  }
  const width = 720;
  const height = 300;
  const margin = { top: 22, right: 24, bottom: 48, left: 64 };
  const times = runs.map((run) => Number(run.median_seconds ?? run.total_seconds));
  const maxTime = Math.max(...times, 0.001);
  const x = (index) => margin.left + (runs.length === 1 ? 0 : index * (width - margin.left - margin.right) / (runs.length - 1));
  const y = (value) => height - margin.bottom - (value / maxTime) * (height - margin.top - margin.bottom);
  const points = runs.map((run, index) => `${x(index)},${y(Number(run.median_seconds ?? run.total_seconds))}`).join(" ");
  svg.innerHTML = `<line x1="${margin.left}" y1="${height - margin.bottom}" x2="${width - margin.right}" y2="${height - margin.bottom}" stroke="#aeb8c8"/>
    <line x1="${margin.left}" y1="${margin.top}" x2="${margin.left}" y2="${height - margin.bottom}" stroke="#aeb8c8"/>
    <polyline points="${points}" fill="none" stroke="#4c5fc7" stroke-width="3"/>
    ${runs.map((run, index) => `<circle cx="${x(index)}" cy="${y(Number(run.median_seconds ?? run.total_seconds))}" r="5" fill="#4c5fc7"><title>${run.workers} workers: ${Number(run.median_seconds ?? run.total_seconds).toFixed(3)} seconds</title></circle>
      <text x="${x(index)}" y="${height - margin.bottom + 22}" text-anchor="middle" fill="#59667a" font-size="12">${run.workers} worker${run.workers === 1 ? "" : "s"}</text>`).join("")}
    <text x="20" y="18" fill="#59667a" font-size="12">Median job time (seconds)</text>
    <text x="${width / 2}" y="${height - 8}" text-anchor="middle" fill="#59667a" font-size="12">Worker count</text>`;
}

async function refreshExperiments() {
  const container = document.querySelector("#experiments-list");
  try {
    const payload = await api("/api/experiments");
    if (!payload.experiments.length) {
      container.innerHTML = '<p class="muted">No benchmark reports found under /data/results. Run tools/benchmark.py to create measured data.</p>';
      renderBenchmarkChart([]);
      return;
    }
    const report = payload.experiments[payload.experiments.length - 1];
    const runs = report.runs || [];
    container.innerHTML = `<p>Dataset: ${escapeHtml(report.dataset_id)} · ${report.document_count} documents · Browser hardware-concurrency hint: ${report.host_logical_cpus ?? "unknown"} · Block size: ${report.block_size} · Threshold: ${report.threshold} · Repeats: ${report.repeats} · Warm-ups per worker count: ${report.warmup_jobs}</p>
      <p>Worker counts: ${escapeHtml(report.configuration?.worker_count_semantics || "configuration not recorded")}</p>
      <p>Timing: ${escapeHtml(report.configuration?.timing || "configuration not recorded")}</p>
      <div class="table-wrap"><table><thead><tr><th>Workers</th><th>Median time (s)</th><th>Pairs/s</th><th>Speedup</th><th>Efficiency</th></tr></thead>
      <tbody>${runs.map((run) => `<tr><td>${run.workers}</td><td>${Number(run.median_seconds ?? run.total_seconds).toFixed(3)}</td><td>${Number(run.pairs_per_second).toFixed(2)}</td><td>${Number(run.speedup).toFixed(2)}×</td><td>${(Number(run.efficiency) * 100).toFixed(1)}%</td></tr>`).join("")}</tbody></table></div>`;
    renderBenchmarkChart(runs);
  } catch (error) {
    container.innerHTML = `<p class="muted">Experiment reports unavailable: ${escapeHtml(error.message)}</p>`;
    renderBenchmarkChart([]);
  }
}

async function initialize() {
  await refreshApiHealth();
  try {
    await refreshDatasets();
    await refreshJobs();
  } catch (error) {
    showError(error);
  }
  refreshSystem().catch(() => {});
  refreshExperiments();
}

initialize();
window.setInterval(() => {
  refreshApiHealth();
  if (state.activeJobId) refreshJobStatus().catch(() => {});
  if (document.querySelector("#tab-system").classList.contains("active")) refreshSystem().catch(() => {});
  if (document.querySelector("#tab-demo").classList.contains("active")) refreshSystem().catch(() => {});
  if (document.querySelector("#tab-timeline").classList.contains("active")) refreshTimeline().catch(() => {});
  if (document.querySelector("#tab-results").classList.contains("active")) refreshResults().catch(() => {});
  if (document.querySelector("#tab-experiments").classList.contains("active")) refreshExperiments();
}, 1000);
