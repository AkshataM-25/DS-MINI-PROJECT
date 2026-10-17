import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowDownWideNarrow,
  ArrowUpWideNarrow,
  Boxes,
  Check,
  ChevronRight,
  CircleHelp,
  Clock3,
  Download,
  FileSearch,
  Files,
  GitCompareArrows,
  LayoutDashboard,
  Menu,
  Network,
  Play,
  RefreshCw,
  Server,
  ShieldAlert,
  Timer,
  Waypoints,
  type LucideIcon,
} from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import "./dashboard.css";

type Page =
  | "Overview"
  | "Submit job"
  | "Job progress"
  | "Comparison results"
  | "Worker monitor"
  | "Architecture"
  | "Benchmarks"
  | "Fault tolerance"
  | "Diagnostics";

type Health = {
  status: string;
  components: Record<string, string>;
  rabbitmq: {
    status: string;
    task_queue_messages?: number;
    task_queue_consumers?: number;
    result_queue_messages?: number;
    error?: string;
  };
  aggregator: { status: string; last_seen: number | null; last_error: string | null };
  worker_count: number;
  healthy_worker_count: number;
  historical_worker_count: number;
};

type Corpus = {
  documents: string[];
  document_count: number;
  pair_count: number;
  source: string;
};

type Job = {
  job_id: string;
  document_count: number;
  expected_tasks: number;
  submitted_tasks: number;
  completed_tasks: number | null;
  failed_tasks: number | null;
  queued_tasks: number | null;
  in_progress_tasks: number | null;
  pending_tasks: number | null;
  results_saved: number;
  similar_pairs: number;
  threshold: number;
  status: string;
  progress?: number;
  created_at: number;
  submitted_at: number | null;
  completed_at: number | null;
  submission_elapsed_seconds?: number | null;
  total_elapsed_seconds?: number | null;
  error?: string | null;
};

type Worker = {
  worker_id: string;
  last_seen: number;
  current_task: string | null;
  tasks_processed: number;
  tasks_failed: number;
  last_error: string | null;
  healthy: boolean;
  seconds_since_seen: number;
};

type Result = {
  task_id: string;
  job_id: string;
  document_a: string;
  document_b: string;
  similarity: number;
  threshold: number;
  status: string;
};

type ResultsResponse = {
  results: Result[];
  results_saved: number;
  similar_pairs: number;
  filtered_count: number;
  status: string;
  limit: number;
  offset: number;
};

type Baseline = {
  documents: number;
  comparisons: number;
  similar_pairs: number;
  threshold: number;
  elapsed_seconds: number;
  result_fingerprint: string;
  algorithm: string;
};

type JobIntegrity = {
  job_id: string;
  complete: boolean;
  result_fingerprint: string | null;
};

const navigation: { label: Page; icon: LucideIcon }[] = [
  { label: "Overview", icon: LayoutDashboard },
  { label: "Submit job", icon: Play },
  { label: "Job progress", icon: Activity },
  { label: "Comparison results", icon: GitCompareArrows },
  { label: "Worker monitor", icon: Network },
  { label: "Architecture", icon: Waypoints },
  { label: "Benchmarks", icon: Boxes },
  { label: "Fault tolerance", icon: ShieldAlert },
  { label: "Diagnostics", icon: Server },
];

async function api<T>(
  path: string,
  init?: RequestInit,
  timeoutMs = 15000,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch {
    throw new Error("The API could not be reached. Start the backend and retry.");
  }
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    if (!response.ok) {
      throw new Error(`API request failed with status ${response.status}.`);
    }
    throw new Error("The API returned an invalid response.");
  }
  if (typeof payload !== "object" || payload === null) {
    throw new Error("The API returned an invalid response.");
  }
  if (!response.ok) {
    const detail =
      typeof payload === "object" &&
      payload !== null &&
      "detail" in payload &&
      typeof payload.detail === "string"
        ? payload.detail
        : `API request failed with status ${response.status}.`;
    throw new Error(detail);
  }
  return payload as T;
}

function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return "Not measured";
  return seconds < 60 ? `${seconds.toFixed(2)} s` : `${(seconds / 60).toFixed(2)} min`;
}

function elapsedSeconds(job: Job): number {
  return job.total_elapsed_seconds ?? Math.max(0, Date.now() / 1000 - job.created_at);
}

function statusTone(status: string | undefined): string {
  if (!status) return "unknown";
  if (["healthy", "completed", "similar"].includes(status)) return "good";
  if (["processing", "queued", "submitting"].includes(status)) return "active";
  if (["degraded", "completed_with_errors", "submission_failed"].includes(status)) return "warn";
  if (["unavailable", "failed", "result mismatch"].includes(status)) return "bad";
  return "unknown";
}

function StatusBadge({ status }: { status: string | undefined }) {
  return <span className={`status-badge ${statusTone(status)}`}>{status ?? "Unknown"}</span>;
}

function Metric({
  label,
  value,
  detail,
  icon: Icon,
}: {
  label: string;
  value: string | number;
  detail: string;
  icon: LucideIcon;
}) {
  return (
    <article className="metric-card">
      <div className="metric-label">
        <span>{label}</span>
        <Icon size={18} aria-hidden="true" />
      </div>
      <strong className="metric-value">{value}</strong>
      <span className="metric-detail">{detail}</span>
    </article>
  );
}

function App() {
  const [page, setPage] = useState<Page>("Overview");
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [health, setHealth] = useState<Health | null>(null);
  const [lastUpdated, setLastUpdated] = useState<number | null>(null);
  const [corpus, setCorpus] = useState<Corpus | null>(null);
  const [workers, setWorkers] = useState<Worker[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [jobId, setJobId] = useState(() => localStorage.getItem("similardocs-job") ?? "");
  const [currentJob, setCurrentJob] = useState<Job | null>(null);
  const [results, setResults] = useState<Result[]>([]);
  const [resultsMeta, setResultsMeta] = useState<ResultsResponse | null>(null);
  const [integrity, setIntegrity] = useState<JobIntegrity | null>(null);
  const [resultsKey, setResultsKey] = useState("");
  const [resultsFailedKey, setResultsFailedKey] = useState("");
  const [minimumScore, setMinimumScore] = useState(0);
  const [resultsPage, setResultsPage] = useState(0);
  const [resultsPageSize, setResultsPageSize] = useState(50);
  const [search, setSearch] = useState("");
  const [ascending, setAscending] = useState(false);
  const [baseline, setBaseline] = useState<Baseline | null>(null);
  const [threshold, setThreshold] = useState("0.10");
  const [faultWorkerIndex, setFaultWorkerIndex] = useState("1");
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [runningBaseline, setRunningBaseline] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const submissionLock = useRef(false);
  const systemRefreshLock = useRef(false);

  const refreshSystem = useCallback(async () => {
    if (systemRefreshLock.current) return;
    systemRefreshLock.current = true;
    const [healthResult, corpusResult, jobsResult, workersResult] = await Promise.allSettled([
      api<Health>("/health"),
      api<Corpus>("/corpus"),
      api<{ jobs: Job[] }>("/jobs?limit=20"),
      api<{ workers: Worker[] }>("/workers"),
    ]);
    if (healthResult.status === "fulfilled") setHealth(healthResult.value);
    else setHealth(null);
    if (corpusResult.status === "fulfilled") setCorpus(corpusResult.value);
    else setCorpus(null);
    if (jobsResult.status === "fulfilled") {
      setJobs(jobsResult.value.jobs);
      setJobId((current) => {
        const id = jobsResult.value.jobs.some((job) => job.job_id === current)
          ? current
          : jobsResult.value.jobs[0]?.job_id || "";
        if (id) localStorage.setItem("similardocs-job", id);
        return id;
      });
    } else setJobs([]);
    if (workersResult.status === "fulfilled") setWorkers(workersResult.value.workers);
    else setWorkers([]);
    if (healthResult.status === "fulfilled") setLastUpdated(Date.now());
    const failures = [healthResult, corpusResult, jobsResult, workersResult].filter(
      (result) => result.status === "rejected",
    );
    if (failures.length) {
      const first = failures[0];
      setError(first.status === "rejected" ? first.reason.message : "API request failed.");
    } else {
      setError("");
    }
    setLoading(false);
    systemRefreshLock.current = false;
  }, []);

  useEffect(() => {
    const initial = window.setTimeout(() => void refreshSystem(), 0);
    const timer = window.setInterval(() => void refreshSystem(), 10000);
    return () => {
      window.clearTimeout(initial);
      window.clearInterval(timer);
    };
  }, [refreshSystem]);

  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;
    const readJob = async () => {
      try {
        const job = await api<Job>(`/jobs/${encodeURIComponent(jobId)}`);
        if (!cancelled) {
          setCurrentJob(job);
          setJobs((existing) => [job, ...existing.filter((item) => item.job_id !== job.job_id)]);
        }
      } catch (problem) {
        if (!cancelled) {
          setError(problem instanceof Error ? problem.message : "Could not load job.");
        }
      }
    };
    void readJob();
    if (["queued", "submitting", "processing"].includes(currentJob?.status ?? "")) {
      const timer = window.setInterval(() => void readJob(), 2500);
      return () => {
        cancelled = true;
        window.clearInterval(timer);
      };
    }
    return () => {
      cancelled = true;
    };
  }, [jobId, currentJob?.status]);

  useEffect(() => {
    if (!jobId || currentJob) return;
    let cancelled = false;
    const timer = window.setInterval(() => {
      void api<Job>(`/jobs/${encodeURIComponent(jobId)}`)
        .then((job) => {
          if (!cancelled) setCurrentJob(job);
        })
        .catch((problem: unknown) => {
          if (!cancelled) {
            setError(problem instanceof Error ? problem.message : "Could not load job.");
          }
        });
    }, 10000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [currentJob, jobId]);

  useEffect(() => {
    if (!jobId || page !== "Comparison results") return;
    let cancelled = false;
    const parameters = new URLSearchParams({
      minimum_score: String(minimumScore),
      limit: String(resultsPageSize),
      offset: String(resultsPage * resultsPageSize),
      sort_order: ascending ? "asc" : "desc",
      search,
    });
    void api<ResultsResponse>(
      `/jobs/${encodeURIComponent(jobId)}/results?${parameters.toString()}`,
    )
      .then((response) => {
        if (!cancelled) {
          setResults(response.results);
          setResultsMeta(response);
          setResultsFailedKey("");
          setResultsKey(
            `${jobId}:${minimumScore}:${resultsPage}:${resultsPageSize}:${ascending}:${search}`,
          );
        }
      })
      .catch((problem: unknown) => {
        if (!cancelled) {
          setResultsFailedKey(
            `${jobId}:${minimumScore}:${resultsPage}:${resultsPageSize}:${ascending}:${search}`,
          );
          setError(problem instanceof Error ? problem.message : "Could not load results.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [
    ascending,
    currentJob?.completed_tasks,
    jobId,
    minimumScore,
    page,
    resultsPage,
    resultsPageSize,
    search,
  ]);

  useEffect(() => {
    if (page !== "Benchmarks" || !jobId || currentJob?.status !== "completed") return;
    let cancelled = false;
    void api<JobIntegrity>(`/jobs/${encodeURIComponent(jobId)}/integrity`)
      .then((response) => {
        if (!cancelled) setIntegrity(response);
      })
      .catch((problem: unknown) => {
        if (!cancelled) {
          setError(problem instanceof Error ? problem.message : "Could not verify results.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [currentJob?.status, jobId, page]);

  const selectJob = (id: string) => {
    setJobId(id);
    setCurrentJob(null);
    localStorage.setItem("similardocs-job", id);
  };

  const submitJob = async () => {
    if (submissionLock.current) return;
    const parsedThreshold = Number(threshold);
    if (!Number.isFinite(parsedThreshold) || parsedThreshold < 0 || parsedThreshold > 1) {
      setError("Similarity threshold must be between 0 and 1.");
      return;
    }
    submissionLock.current = true;
    setSubmitting(true);
    setError("");
    setNotice("");
    try {
      const submitted = await api<{
        job_id: string;
        expected_tasks: number;
        tasks_submitted: number;
        status: string;
      }>("/jobs", {
        method: "POST",
        body: JSON.stringify({ threshold: parsedThreshold }),
      }, 600000);
      selectJob(submitted.job_id);
      setNotice(
          `Job started. ${submitted.tasks_submitted.toLocaleString()} of ${submitted.expected_tasks.toLocaleString()} tasks submitted.`,
      );
      setPage("Job progress");
      await refreshSystem();
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : "Job submission failed.");
    } finally {
      submissionLock.current = false;
      setSubmitting(false);
    }
  };

  const runBaseline = async () => {
    setRunningBaseline(true);
    setError("");
    try {
      const benchmarkThreshold = currentJob?.threshold ?? Number(threshold);
      setBaseline(
        await api<Baseline>("/benchmarks/baseline", {
          method: "POST",
          body: JSON.stringify({ threshold: benchmarkThreshold }),
        }),
      );
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : "Could not run the baseline.");
    } finally {
      setRunningBaseline(false);
    }
  };

  const copyFaultCommand = async () => {
    if (!faultEligibleJob) {
      setError("Select a queued or processing job first.");
      return;
    }
    const command = `python tools/fault_tolerance_demo.py --job-id ${faultEligibleJob.job_id} --worker-index ${faultWorkerIndex} --confirm YES`;
    try {
      await navigator.clipboard.writeText(command);
      setNotice("Command copied. Review it before running; it stops one worker.");
      setError("");
    } catch {
      setError("Could not copy. Copy the command shown on screen.");
    }
  };

  const visibleResults = useMemo(() => {
    const key = `${jobId}:${minimumScore}:${resultsPage}:${resultsPageSize}:${ascending}:${search}`;
    return resultsKey === key ? results : [];
  }, [ascending, jobId, minimumScore, results, resultsKey, resultsPage, resultsPageSize, search]);

  const pageTitle = page;
  const healthyWorkers = workers.filter((worker) => worker.healthy).length;
  const processingJob = jobId ? currentJob : null;
  const currentIntegrity = integrity?.job_id === jobId ? integrity : null;
  const faultEligibleJob = jobs.find(
    (job) =>
      job.job_id === jobId && ["queued", "processing"].includes(job.status),
  );
  const resultsKeyForCurrentPage =
    `${jobId}:${minimumScore}:${resultsPage}:${resultsPageSize}:${ascending}:${search}`;
  const visibleResultsMeta =
    resultsKey === resultsKeyForCurrentPage ? resultsMeta : null;
  const resultsLoading =
    resultsKey !== resultsKeyForCurrentPage &&
    resultsFailedKey !== resultsKeyForCurrentPage;
  const resultPageCount = Math.max(
    1,
    Math.ceil((visibleResultsMeta?.filtered_count ?? 0) / resultsPageSize),
  );
  const benchmarkOutputsMatch = Boolean(
    baseline &&
      currentIntegrity?.complete &&
      processingJob &&
      baseline.documents === processingJob.document_count &&
      baseline.threshold === processingJob.threshold &&
      processingJob.results_saved === processingJob.expected_tasks &&
      baseline.result_fingerprint === currentIntegrity.result_fingerprint,
  );

  return (
    <div className="app-shell">
      {mobileNavOpen && <button className="nav-scrim" aria-label="Close navigation" onClick={() => setMobileNavOpen(false)} />}
      <aside className={`sidebar ${mobileNavOpen ? "sidebar-open" : ""}`}>
        <a className="brand" href="#overview" onClick={(event) => { event.preventDefault(); setPage("Overview"); setMobileNavOpen(false); }}>
          <span className="brand-mark"><FileSearch size={22} /></span>
          <span><strong>SimilarDocs</strong><small>Document similarity checker</small></span>
        </a>
        <div className="nav-caption">WORKSPACE</div>
        <nav className="navigation" aria-label="Main navigation">
          {navigation.map(({ label, icon: Icon }) => (
            <button
              className={`nav-item ${page === label ? "selected" : ""}`}
              key={label}
              onClick={() => { setPage(label); setMobileNavOpen(false); }}
              type="button"
              aria-current={page === label ? "page" : undefined}
            >
              <Icon size={17} /><span>{label}</span>
              {label === "Worker monitor" && <span className="nav-count">{health?.worker_count ?? "—"}</span>}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="sidebar-health">
            <span className={`health-indicator ${statusTone(health?.status)}`} />
            <div><strong>{health?.status ?? "Unknown"}</strong><small>{health ? `${health.worker_count} workers consuming` : "Waiting for API"}</small></div>
          </div>
          <p className="sidebar-note">Synthetic test documents</p>
        </div>
      </aside>

      <main className="main-content">
        <header className="topbar">
          <div className="topbar-left">
            <button className="icon-button mobile-menu-button" type="button" onClick={() => setMobileNavOpen(true)} aria-label="Open navigation"><Menu size={19} /></button>
            <div className="breadcrumbs">SimilarDocs <ChevronRight size={14} /> <strong>{pageTitle}</strong></div>
          </div>
          <div className="topbar-actions">
            <span className="last-updated">{health ? `Updated ${lastUpdated ? new Date(lastUpdated).toLocaleTimeString() : "just now"}` : "API unavailable"}</span>
            <StatusBadge status={health?.status} />
            <button className="icon-button" type="button" onClick={() => void refreshSystem()} aria-label="Refresh status" title="Refresh live system data">
              <RefreshCw size={16} />
            </button>
          </div>
        </header>

        <section className="page-content">
          <div className="page-heading">
            <div>
              <h1>{pageTitle}</h1>
              <p>{page === "Overview"
                ? "Document comparison status."
                : page === "Submit job"
                  ? "Choose a similarity threshold and start a comparison."
                  : "Current job and service status."}</p>
            </div>
            {page !== "Submit job" && (
              <button className="primary-button" type="button" onClick={() => setPage("Submit job")} disabled={submitting}>
                <GitCompareArrows size={17} /> Submit comparison
              </button>
            )}
          </div>

          {error && <div className="alert error-alert" role="alert"><AlertTriangle size={18} />{error}</div>}
          {notice && <div className="alert success-alert" role="status"><Check size={18} />{notice}</div>}

          {page === "Overview" && (
            <>
              <div className="notice">
                <CircleHelp size={19} />
                <p>Scores show text overlap, not proof of plagiarism. Review the documents before drawing conclusions.</p>
              </div>
              <div className="metric-grid">
                <Metric label="Demo documents" value={corpus?.document_count.toLocaleString() ?? "—"} detail={corpus?.source ?? "Corpus not measured"} icon={Files} />
                <Metric label="Possible comparisons" value={corpus?.pair_count.toLocaleString() ?? "—"} detail="N × (N − 1) / 2" icon={GitCompareArrows} />
                <Metric label="Saved comparisons" value={processingJob?.results_saved.toLocaleString() ?? "—"} detail={processingJob ? `${processingJob.completed_tasks ?? 0} successful tasks` : "No selected job"} icon={Check} />
                <Metric label="Pairs above threshold" value={processingJob?.similar_pairs?.toLocaleString() ?? "—"} detail={processingJob ? `Threshold ≥ ${processingJob.threshold.toFixed(2)}` : "No selected job"} icon={FileSearch} />
              </div>
              <div className="two-column">
                <section className="panel">
                  <div className="panel-heading"><div><h2>Selected job</h2><p>Current task progress.</p></div><StatusBadge status={processingJob?.status} /></div>
                  {processingJob ? (
                    <>
                      <div className="job-id">{processingJob.job_id}</div>
                      <Progress job={processingJob} />
                      <div className="stat-list">
                        <span>Submitted <strong>{processingJob.submitted_tasks.toLocaleString()}</strong></span>
                        <span>Results saved <strong>{processingJob.results_saved.toLocaleString()}</strong></span>
                        <span>Failed <strong>{(processingJob.failed_tasks ?? 0).toLocaleString()}</strong></span>
                        <span>Elapsed <strong>{formatDuration(elapsedSeconds(processingJob))}</strong></span>
                      </div>
                    </>
                  ) :                   <EmptyState text="No job yet. Start a comparison to see progress." />}
                </section>
                <section className="panel">
                  <div className="panel-heading"><div><h2>Services</h2><p>Current service status.</p></div><Activity size={18} /></div>
                  <ServiceRow name="FastAPI" status={health?.components.api} detail="HTTP API response" />
                  <ServiceRow name="RabbitMQ" status={health?.components.rabbitmq} detail={health?.rabbitmq.task_queue_messages == null ? "Queue depth not measured" : `${health.rabbitmq.task_queue_messages} ready task messages`} />
                  <ServiceRow name="Results aggregator" status={health?.components.aggregator} detail="Marked offline if no update for 15 seconds" />
                  <ServiceRow name="Workers" status={health?.components.workers} detail={`${health?.worker_count ?? "Unknown"} active RabbitMQ consumers · ${health?.historical_worker_count ?? "Unknown"} known identities`} />
                </section>
              </div>
              <div className="two-column">
                <section className="panel">
                  <div className="panel-heading"><div><h2>Worker activity</h2><p>Completed tasks by worker.</p></div><Activity size={19} /></div>
                  {workers.some((worker) => worker.healthy) ? (
                    <div className="chart-wrap" role="img" aria-label="Successful task counts for active workers">
                      <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={workers.filter((worker) => worker.healthy)} margin={{ top: 8, right: 8, left: -16, bottom: 4 }}>
                          <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e9edf4" />
                          <XAxis dataKey="worker_id" tickFormatter={(value: string) => value.slice(0, 7)} tick={{ fontSize: 11 }} />
                          <YAxis allowDecimals={false} tick={{ fontSize: 11 }} />
                          <Tooltip formatter={(value) => [Number(value).toLocaleString(), "Tasks processed"]} labelFormatter={(value) => `Worker ${value}`} />
                          <Bar dataKey="tasks_processed" name="Successful tasks" fill="#5b68db" radius={[5, 5, 0, 0]} />
                        </BarChart>
                      </ResponsiveContainer>
                    </div>
                  ) : <EmptyState text="No recent worker activity." />}
                </section>
                <section className="panel quick-actions-panel">
                  <div className="panel-heading"><div><h2>Quick actions</h2></div><GitCompareArrows size={19} /></div>
                  <button className="quick-action" type="button" onClick={() => setPage("Submit job")}><Play size={17} /><span><strong>Compare documents</strong><small>Start a comparison</small></span><ChevronRight size={17} /></button>
                  <button className="quick-action" type="button" onClick={() => setPage("Comparison results")} disabled={!jobId}><FileSearch size={17} /><span><strong>View results</strong><small>Filter or export pairs</small></span><ChevronRight size={17} /></button>
                  <button className="quick-action" type="button" onClick={() => setPage("Diagnostics")}><Server size={17} /><span><strong>System status</strong><small>Check services and workers</small></span><ChevronRight size={17} /></button>
                </section>
              </div>
              <section className="panel">
                <div className="panel-heading"><div><h2>Recent jobs</h2></div><button className="text-action" type="button" onClick={() => setPage("Job progress")}>View all <ChevronRight size={15} /></button></div>
                <JobTable jobs={jobs.slice(0, 8)} onSelect={(id) => { selectJob(id); setPage("Job progress"); }} />
              </section>
            </>
          )}

          {page === "Submit job" && (
            <section className="panel narrow-panel">
              <div className="panel-heading"><div><h2>Compare documents</h2><p>Compare each document pair in the available corpus.</p></div><Files size={19} /></div>
              <div className="corpus-summary">
                <div><span>Available documents</span><strong>{corpus?.document_count.toLocaleString() ?? "Unknown"}</strong></div>
                <div><span>Generated tasks</span><strong>{corpus?.pair_count.toLocaleString() ?? "Unknown"}</strong></div>
                <div><span>Input source</span><strong>Synthetic demo corpus</strong></div>
              </div>
              {corpus && <p className="corpus-preview"><strong>Mounted files:</strong> {corpus.documents.slice(0, 5).join(", ")}{corpus.documents.length > 5 ? ` and ${corpus.documents.length - 5} more` : ""}</p>}
              {!loading && corpus?.document_count != null && corpus.document_count < 2 && (
                <div className="alert error-alert"><AlertTriangle size={17} />At least two doc_*.txt files are required.</div>
              )}
              <label className="field-label" htmlFor="threshold">Similarity threshold <span className="field-unit">(0.00–1.00)</span></label>
              <input id="threshold" className="text-input threshold-input" type="number" min="0" max="1" step="0.01" inputMode="decimal" value={threshold} aria-describedby="threshold-help" onChange={(event) => setThreshold(event.target.value)} />
              <p id="threshold-help" className="field-help">Pairs at or above this score are marked similar.</p>
              <div className="notice"><CircleHelp size={18} /><p>Scores show text overlap, not proof of plagiarism. Uploads are disabled; this page uses the available corpus.</p></div>
              <button className="primary-button" type="button" disabled={submitting || !corpus || corpus.document_count < 2 || health?.rabbitmq.status !== "healthy"} onClick={() => void submitJob()}>
                {submitting ? <RefreshCw size={16} className="spin" /> : <Play size={16} />}
                {submitting ? "Submitting tasks…" : "Start comparison job"}
              </button>
              {health?.rabbitmq.status !== "healthy" && <p className="field-help warning-text">RabbitMQ is unavailable. Check Diagnostics before starting a job.</p>}
            </section>
          )}

          {page === "Job progress" && (
            <section className="panel">
              <div className="panel-heading"><div><h2>Job progress</h2></div><StatusBadge status={processingJob?.status} /></div>
              <label className="field-label" htmlFor="job-picker">Tracked job</label>
              <select id="job-picker" className="text-input job-picker" value={jobId} onChange={(event) => selectJob(event.target.value)}>
                <option value="">Select a job</option>
                {jobs.map((job) => <option value={job.job_id} key={job.job_id}>{job.job_id} · {job.status}</option>)}
              </select>
              {processingJob ? (
                <div className="progress-detail">
                  <div className="progress-title"><span>{processingJob.job_id}</span><strong>{Math.round((processingJob.progress ?? 0) * 100)}%</strong></div>
                  <Progress job={processingJob} />
                  <div className="metric-grid compact-grid">
                    <Metric label="Expected" value={processingJob.expected_tasks.toLocaleString()} detail={`${processingJob.document_count} documents`} icon={GitCompareArrows} />
                    <Metric label="Submitted" value={processingJob.submitted_tasks.toLocaleString()} detail={formatDuration(processingJob.submission_elapsed_seconds)} icon={Play} />
                    <Metric label="Completed" value={(processingJob.completed_tasks ?? 0).toLocaleString()} detail={`${processingJob.results_saved.toLocaleString()} successful results saved`} icon={Check} />
                    <Metric label="In progress" value={(processingJob.in_progress_tasks ?? 0).toLocaleString()} detail={`${(processingJob.queued_tasks ?? 0).toLocaleString()} ready in task state`} icon={Timer} />
                  </div>
                  <div className="stat-list">
                    <span>Pending publication <strong>{(processingJob.pending_tasks ?? 0).toLocaleString()}</strong></span>
                    <span>Queued <strong>{(processingJob.queued_tasks ?? 0).toLocaleString()}</strong></span>
                    <span>Terminal failures <strong>{(processingJob.failed_tasks ?? 0).toLocaleString()}</strong></span>
                    <span>Similar pairs <strong>{processingJob.similar_pairs.toLocaleString()}</strong></span>
                    <span>End-to-end elapsed <strong>{formatDuration(elapsedSeconds(processingJob))}</strong></span>
                  </div>
                  {processingJob.error && <p className="warning-text">Submission error: {processingJob.error}</p>}
                </div>
              ) : <EmptyState text={jobId ? "Loading job state or job not found." : "Submit a job to track its progress."} />}
            </section>
          )}

          {page === "Comparison results" && (
            <section className="panel">
              <div className="panel-heading">
              <div><h2>Saved comparison results</h2><p>{jobId || "Select a job"} · {visibleResultsMeta ? `${visibleResultsMeta.filtered_count.toLocaleString()} matching of ${visibleResultsMeta.results_saved.toLocaleString()} saved` : "Select a job to inspect its saved comparisons"}</p></div>
                {jobId && <a className="secondary-button" href={`/api/jobs/${encodeURIComponent(jobId)}/results.csv`}><Download size={16} /> Download CSV</a>}
              </div>
              {!jobId ? <EmptyState text="Submit or select a job to view its results." /> : (
                <>
                  <label className="field-label" htmlFor="results-job-picker">Results for job</label>
                  <select id="results-job-picker" className="text-input job-picker" value={jobId} onChange={(event) => { setResultsPage(0); selectJob(event.target.value); }}>
                    {jobs.map((job) => <option value={job.job_id} key={job.job_id}>{job.job_id} · {job.status}</option>)}
                  </select>
                  <div className="table-controls">
                    <label className="search-control" htmlFor="results-search">Search document names<input id="results-search" className="text-input search-input" placeholder="For example, doc_001" value={search} onChange={(event) => { setSearch(event.target.value); setResultsPage(0); }} /></label>
                    <label className="inline-field">Minimum score <input className="text-input score-input" type="number" min="0" max="1" step="0.01" value={minimumScore} onChange={(event) => { setMinimumScore(Number(event.target.value)); setResultsPage(0); }} /></label>
                    <label className="inline-field">Rows per page<select className="text-input score-input" value={resultsPageSize} onChange={(event) => { setResultsPageSize(Number(event.target.value)); setResultsPage(0); }}><option value={25}>25</option><option value={50}>50</option><option value={100}>100</option><option value={250}>250</option></select></label>
                    <button className="secondary-button" type="button" aria-label={ascending ? "Sort highest similarity first" : "Sort lowest similarity first"} onClick={() => { setAscending((value) => !value); setResultsPage(0); }}>
                      {ascending ? <ArrowUpWideNarrow size={16} /> : <ArrowDownWideNarrow size={16} />}
                      {ascending ? "Lowest first" : "Highest first"}
                    </button>
                  </div>
                  <div className="table-wrap"><table><thead><tr><th>Document A</th><th>Document B</th><th>Similarity</th><th>Threshold</th><th>Status</th></tr></thead>
                    <tbody>{visibleResults.map((result) => <tr key={result.task_id}>
                      <td>{result.document_a}</td><td>{result.document_b}</td><td className="score-value">{result.similarity.toFixed(4)}</td>
                      <td><StatusBadge status={result.similarity >= result.threshold ? "similar" : "below threshold"} /></td><td><StatusBadge status={result.status} /></td>
                    </tr>)}
                    {!visibleResults.length && <tr><td colSpan={5} className="empty-cell">{resultsLoading ? "Loading saved results…" : visibleResultsMeta?.status && !["completed", "completed_with_errors"].includes(visibleResultsMeta.status) ? "No results saved yet; this job may still be running." : "No results match this search and score filter."}</td></tr>}
                    </tbody></table></div>
                  {visibleResultsMeta && visibleResultsMeta.filtered_count > 0 && <div className="pagination-bar"><span>Showing {resultsPage * resultsPageSize + 1}–{Math.min((resultsPage + 1) * resultsPageSize, visibleResultsMeta.filtered_count)} of {visibleResultsMeta.filtered_count.toLocaleString()} matches</span><div><button className="secondary-button" type="button" disabled={resultsPage === 0 || resultsLoading} onClick={() => setResultsPage((value) => Math.max(0, value - 1))}>Previous</button><span className="page-number">Page {resultsPage + 1} of {resultPageCount}</span><button className="secondary-button" type="button" disabled={resultsPage + 1 >= resultPageCount || resultsLoading} onClick={() => setResultsPage((value) => Math.min(resultPageCount - 1, value + 1))}>Next</button></div></div>}
                  <p className="field-help">Results are shown page by page. Download the CSV to see all saved comparisons.</p>
                </>
              )}
            </section>
          )}

          {page === "Worker monitor" && (
            <section className="panel">
              <div className="panel-heading"><div><h2>Worker processes</h2><p>Active means a fresh heartbeat and an active RabbitMQ consumer. Old identities remain listed as history.</p></div><StatusBadge status={health?.worker_count ? "healthy" : workers.length ? "unavailable" : undefined} /></div>
              <div className="metric-grid worker-summary-grid">
                <Metric label="Active consumers" value={health?.worker_count ?? "Unknown"} detail="Broker task-queue consumer count, capped by fresh heartbeats" icon={Network} />
                <Metric label="Healthy heartbeats" value={healthyWorkers} detail="Seen within the 15-second health window" icon={Activity} />
                <Metric label="Known identities" value={health?.historical_worker_count ?? workers.length} detail="Includes workers from previous Compose runs" icon={Server} />
                <Metric label="Ready tasks" value={health?.rabbitmq.task_queue_messages?.toLocaleString() ?? "Unknown"} detail="RabbitMQ ready depth; not unacknowledged deliveries" icon={Boxes} />
              </div>
              {!workers.length ? <EmptyState text="No worker heartbeat has been observed. Compose configuration alone does not prove a worker is running." /> : (
                <div className="table-wrap"><table><thead><tr><th>Worker / container</th><th>Health</th><th>Current task</th><th>Successful tasks</th><th>Failed attempts</th><th>Last seen</th></tr></thead><tbody>
                  {workers.map((worker) => <tr key={worker.worker_id}>
                    <td className="worker-name"><Network size={15} />{worker.worker_id}</td>
                    <td><StatusBadge status={worker.healthy && (health?.rabbitmq.task_queue_consumers ?? 0) > 0 ? "healthy" : "unavailable"} /></td>
                    <td className="mono">{worker.healthy ? worker.current_task ?? "Idle" : "Not current (stale heartbeat)"}</td>
                    <td>{worker.tasks_processed.toLocaleString()}</td><td>{worker.tasks_failed.toLocaleString()}</td>
                    <td>{worker.healthy ? `${worker.seconds_since_seen.toFixed(1)} s ago` : `${(worker.seconds_since_seen / 60).toFixed(1)} min ago`}</td>
                  </tr>)}
                </tbody></table></div>
              )}
              <p className="field-help">Task totals are lifetime worker counters, not broker delivery counters. The aggregator is authoritative for persisted results. A heartbeat alone is not treated as proof the worker is consuming.</p>
            </section>
          )}

          {page === "Architecture" && (
            <section className="panel">
              <div className="panel-heading"><div><h2>How a job moves</h2><p>The API sends tasks to RabbitMQ. Workers compare the documents and return scores.</p></div><Waypoints size={18} /></div>
              <div className="architecture-flow">
                <ArchitectureNode name="Dashboard" detail="Shows job status and results" status="healthy" icon={LayoutDashboard} />
                <FlowArrow label="HTTP /api" />
                <ArchitectureNode name="FastAPI" detail="Accepts jobs and serves results" status={health?.components.api} icon={Server} />
                <FlowArrow label="publish persistent tasks" />
                <ArchitectureNode name="RabbitMQ · comparison_tasks" detail={health?.rabbitmq.task_queue_messages == null ? "Waiting task count unavailable" : `${health.rabbitmq.task_queue_messages} tasks waiting`} status={health?.rabbitmq.status} icon={Boxes} />
                <FlowArrow label="consume / ack" />
                <ArchitectureNode name={`Workers · ${health?.worker_count ?? "Unknown"} running`} detail={`${healthyWorkers} reporting recently`} status={health?.components.workers} icon={Network} />
                <FlowArrow label="publish comparison_results" />
                <ArchitectureNode name="RabbitMQ · comparison_results" detail={health?.rabbitmq.result_queue_messages == null ? "Waiting result count unavailable" : `${health.rabbitmq.result_queue_messages} results waiting`} status={health?.rabbitmq.status} icon={Boxes} />
                <FlowArrow label="consume / persist / ack" />
                <ArchitectureNode name="Aggregator" detail="Saves comparison results" status={health?.components.aggregator} icon={Activity} />
                <FlowArrow label="saves job and result data" />
                <ArchitectureNode name="Results store" detail="Jobs, tasks, scores, and workers" status={health ? "healthy" : undefined} icon={Files} />
                <FlowArrow label="API reads job data" />
                <ArchitectureNode name="FastAPI" detail="Returns status and CSV reports" status={health?.components.api} icon={Server} />
                <FlowArrow label="returns status and results" />
                <ArchitectureNode name="Dashboard" detail="Displays job progress and results" status="healthy" icon={FileSearch} />
              </div>
              <p className="field-help">Queue counts show waiting messages. Active deliveries are not measured here.</p>
            </section>
          )}

          {page === "Benchmarks" && (
            <>
            <section className="panel benchmark-job-picker">
              <label className="field-label" htmlFor="benchmark-job">Distributed job to compare</label>
              <select id="benchmark-job" className="text-input job-picker" value={jobId} onChange={(event) => selectJob(event.target.value)}>
                <option value="">Select a job</option>
                {jobs.map((job) => <option value={job.job_id} key={job.job_id}>{job.job_id} · {job.status}</option>)}
              </select>
            </section>
            <div className="two-column">
              <section className="panel">
                <div className="panel-heading"><div><h2>Single-process baseline</h2><p>Runs all corpus pairs with the same shingle/Jaccard functions.</p></div><Clock3 size={18} /></div>
                <button className="primary-button" type="button" disabled={runningBaseline || !corpus || corpus.document_count < 2} onClick={() => void runBaseline()}>
                  {runningBaseline ? <RefreshCw size={16} className="spin" /> : <Play size={16} />}
                  {runningBaseline ? "Measuring baseline…" : "Run baseline now"}
                </button>
                {baseline && <div className="benchmark-result">
                  <div><span>Wall-clock runtime</span><strong>{formatDuration(baseline.elapsed_seconds)}</strong></div>
                  <div><span>Comparisons</span><strong>{baseline.comparisons.toLocaleString()}</strong></div>
                  <div><span>Similar pairs</span><strong>{baseline.similar_pairs.toLocaleString()}</strong></div>
                  <p>{baseline.documents} documents · threshold {baseline.threshold.toFixed(2)}</p>
                </div>}
              </section>
              <section className="panel">
                <div className="panel-heading"><div><h2>Job timing</h2><p>Submission and completion times.</p></div><Network size={18} /></div>
                {processingJob ? (
                  <div className="benchmark-result">
                    <div><span>Job</span><strong className="mono">{processingJob.job_id}</strong></div>
                    <div><span>Submission time</span><strong>{formatDuration(processingJob.submission_elapsed_seconds)}</strong></div>
                    <div><span>{processingJob.completed_at ? "End-to-end time" : "Elapsed so far"}</span><strong>{formatDuration(elapsedSeconds(processingJob))}</strong></div>
                    <div><span>Persisted / expected</span><strong>{processingJob.results_saved.toLocaleString()} / {processingJob.expected_tasks.toLocaleString()}</strong></div>
                    {baseline && (
                      <div>
                        <span>Exact pair-and-score check</span>
                        <strong>
                          {benchmarkOutputsMatch
                            ? "All pair scores match"
                            : currentIntegrity?.complete && processingJob.results_saved === processingJob.expected_tasks
                              ? "Corpus, threshold, or scores differ"
                              : "Not verified / incomplete"}
                        </strong>
                      </div>
                    )}
                    {baseline && processingJob.completed_at && <div><span>Distributed time vs baseline</span><strong>{baseline.elapsed_seconds > 0 ? `${(elapsedSeconds(processingJob) / baseline.elapsed_seconds).toFixed(1)}× baseline runtime` : "Not measured"}</strong></div>}
                    <p>Compare runs using the same documents and threshold.</p>
                  </div>
                ) : <EmptyState text="Submit a job to measure its real distributed completion time." />}
              </section>
              {baseline && processingJob?.completed_at && <section className="panel full-width-panel">
                <div className="panel-heading"><div><h2>Measured wall-clock comparison</h2><p>Real baseline and end-to-end duration for the selected workload. Values update only after a completed distributed job.</p></div><Clock3 size={19} /></div>
                <div className="benchmark-chart" role="img" aria-label="Measured baseline and distributed runtimes in seconds">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={[{ run: "Single-process baseline", seconds: baseline.elapsed_seconds }, { run: "Distributed end-to-end", seconds: elapsedSeconds(processingJob) }]} margin={{ top: 10, right: 18, left: 8, bottom: 4 }}>
                      <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e9edf4" />
                      <XAxis dataKey="run" tick={{ fontSize: 12 }} />
                      <YAxis unit=" s" tick={{ fontSize: 11 }} />
                      <Tooltip formatter={(value) => [`${Number(value).toFixed(2)} s`, "Wall-clock time"]} />
                      <Bar dataKey="seconds" name="Seconds" fill="#5b68db" radius={[6, 6, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
                <p className="field-help">{benchmarkOutputsMatch ? "Results match." : "Results do not match."} Queue and container overhead affect run time.</p>
              </section>}
              <div className="notice full-width-notice"><AlertTriangle size={18} /><p>Queue and container overhead can affect run time.</p></div>
            </div>
            </>
          )}

          {page === "Fault tolerance" && (
            <section className="panel">
              <div className="panel-heading"><div><h2>Worker failure demo</h2><p>Use the command below to stop one worker during a job.</p></div><ShieldAlert size={19} /></div>
              <div className="notice"><AlertTriangle size={18} /><p>Requires an active job, two workers, and a task in progress. The script asks for confirmation and restarts the worker after the test.</p></div>
              <div className="fault-controls">
                <label className="field-label" htmlFor="fault-job">Active job to observe</label>
                <select id="fault-job" className="text-input job-picker" value={faultEligibleJob?.job_id ?? ""} onChange={(event) => selectJob(event.target.value)}>
                  <option value="">Select an active job</option>
                  {jobs.filter((job) => ["queued", "processing"].includes(job.status)).map((job) => <option key={job.job_id} value={job.job_id}>{job.job_id} · {job.status} · {job.completed_tasks}/{job.expected_tasks}</option>)}
                </select>
                <label className="field-label" htmlFor="fault-worker-index">Worker slot from <code>docker compose ps worker</code></label>
                <select id="fault-worker-index" className="text-input threshold-input" value={faultWorkerIndex} onChange={(event) => setFaultWorkerIndex(event.target.value)}>
                  {Array.from({ length: Math.max(health?.worker_count ?? 1, 1) }, (_, index) => <option key={index + 1} value={index + 1}>Worker slot {index + 1}</option>)}
                </select>
              </div>
              <pre className="command-block"><code>{`Set-Location "C:\\Users\\Akshata\\Desktop\\DS MINI PROJECT"
python tools\\fault_tolerance_demo.py --job-id ${faultEligibleJob?.job_id ?? "<select-an-active-job>"} --worker-index ${faultWorkerIndex} --confirm YES`}</code></pre>
              <div className="button-row">
                <button className="primary-button" type="button" onClick={() => void copyFaultCommand()} disabled={!faultEligibleJob}><Files size={16} />Copy command</button>
                <a className="secondary-button" href="http://localhost:15672" target="_blank" rel="noreferrer"><Boxes size={16} />Open RabbitMQ management</a>
              </div>
              <div className="fault-evidence">
                <div><strong>Check after the test</strong><span>Look for a redelivery and confirm the job completes.</span></div>
              </div>
            </section>
          )}

          {page === "Diagnostics" && (
            <div className="two-column">
              <section className="panel">
                <div className="panel-heading"><div><h2>Service readiness</h2><p>Live endpoint and heartbeat results.</p></div><RefreshCw size={17} /></div>
                {["api", "rabbitmq", "aggregator", "workers"].map((name) => <ServiceRow key={name} name={name} status={health?.components[name]} detail={name === "workers" ? `${healthyWorkers} healthy / ${workers.length} detected` : name === "rabbitmq" ? `Task queue ready depth: ${health?.rabbitmq.task_queue_messages ?? "Unknown"}; unacked: Not measured` : name === "aggregator" ? `Last heartbeat: ${health?.aggregator.last_seen ? new Date(health.aggregator.last_seen * 1000).toLocaleTimeString() : "Unknown"}` : "API response received"} />)}
                <p className="field-help">API errors appear above. RabbitMQ errors show the error type only.</p>
              </section>
              <section className="panel">
                <div className="panel-heading"><div><h2>Task and data integrity</h2><p>Current job totals.</p></div><Files size={18} /></div>
                {processingJob ? <div className="integrity-list">
                  <span>Expected tasks <strong>{processingJob.expected_tasks.toLocaleString()}</strong></span>
                  <span>Submitted tasks <strong>{processingJob.submitted_tasks.toLocaleString()}</strong></span>
                  <span>Saved results <strong>{processingJob.results_saved.toLocaleString()}</strong></span>
                  <span>Terminal failures <strong>{(processingJob.failed_tasks ?? 0).toLocaleString()}</strong></span>
                  <span>Still queued / pending <strong>{((processingJob.queued_tasks ?? 0) + (processingJob.pending_tasks ?? 0)).toLocaleString()}</strong></span>
                </div> : <EmptyState text="No job has been selected." />}
                {processingJob?.status === "submission_failed" && <p className="warning-text">RabbitMQ did not confirm all tasks. Check pending tasks before retrying.</p>}
              </section>
            </div>
          )}

          <footer className="page-footer">
            <span>SimilarDocs · Distributed Systems mini-project</span>
            <span>Textual similarity is not proof of plagiarism.</span>
          </footer>
        </section>
      </main>
    </div>
  );
}

function Progress({ job }: { job: Job }) {
  const progress = Math.max(0, Math.min(1, job.progress ?? 0));
  return (
    <div className="progress-track" role="progressbar" aria-valuenow={Math.round(progress * 100)} aria-valuemin={0} aria-valuemax={100} aria-label="Persisted task completion">
      <span style={{ width: `${progress * 100}%` }} />
    </div>
  );
}

function EmptyState({ text }: { text: string }) {
  return <div className="empty-state"><Files size={22} /><p>{text}</p></div>;
}

function ServiceRow({ name, status, detail }: { name: string; status: string | undefined; detail: string }) {
  return <div className="service-row"><div><span className={`health-indicator ${statusTone(status)}`} /><div><strong>{name}</strong><small>{detail}</small></div></div><StatusBadge status={status} /></div>;
}

function JobTable({ jobs, onSelect }: { jobs: Job[]; onSelect: (id: string) => void }) {
  if (!jobs.length) return <EmptyState text="No submitted jobs are recorded yet." />;
  return <div className="table-wrap"><table><thead><tr><th>Job ID</th><th>Status</th><th>Progress</th><th>Similar pairs</th><th>Created</th><th /></tr></thead><tbody>
    {jobs.map((job) => <tr key={job.job_id}><td className="mono">{job.job_id}</td><td><StatusBadge status={job.status} /></td><td>{(job.completed_tasks ?? 0).toLocaleString()} / {job.expected_tasks.toLocaleString()}</td><td>{job.similar_pairs.toLocaleString()}</td><td>{new Date(job.created_at * 1000).toLocaleString()}</td><td><button className="table-action" type="button" onClick={() => onSelect(job.job_id)}>Open <ChevronRight size={14} /></button></td></tr>)}
  </tbody></table></div>;
}

function ArchitectureNode({ name, detail, status, icon: Icon }: { name: string; detail: string; status: string | undefined; icon: LucideIcon }) {
  return <div className={`architecture-node ${statusTone(status)}`}><span className="node-icon"><Icon size={17} /></span><div><strong>{name}</strong><small>{detail}</small></div><StatusBadge status={status} /></div>;
}

function FlowArrow({ label }: { label: string }) {
  return <div className="flow-arrow"><span>{label}</span><i aria-hidden="true" /></div>;
}

export default App;
