# Ten-minute evaluation walkthrough

## Before the evaluation

Start Docker Desktop and launch the project once from the repository root:

```powershell
docker compose up -d --build --scale worker=4
```

Then open <http://localhost:8000/>. The API and RabbitMQ management page are bound to loopback. The local `.env` enables explicitly labelled demo controls. The presentation itself can be run from the dashboard; keep PowerShell closed during the live demo if that is easier.

## Suggested sequence

### 0:00–1:00 — Live services and dataset

Open **System**. Show API, RabbitMQ, aggregator RPC, the four worker replicas, task consumers, queue counts, and the Compose DNS lookup for `worker`. Open RabbitMQ management from its System-page link if you want a second live view.

Open **Dataset**, generate a seeded 200-document corpus, and note that the expected total is `200 × 199 / 2 = 19,900` unique document pairs. Return to **Demo center** and select that dataset.

### 1:00–2:00 — Real distributed comparison and correctness

Open **Run job**, start a normal comparison job, and show its ID, task count, pair count, per-worker contribution, and both integrity checks. Open **Results**, click **Run baseline verification**, then inspect a ranked pair and its highlighted shared shingles. Explain that overlap is not proof of plagiarism.

### 2:00–3:00 — Durable asynchronous queue

In **Demo center**, click **Pause all workers** and wait for the page to report zero task consumers. Click **Submit job to queue**. The System queue should show persistent ready messages while workers remain alive but are not consuming. Click **Resume all workers**; watch the same queue drain and the job complete with valid pair integrity.

### 3:00–4:30 — Measured scalability

With four worker containers already running, select the 200-document dataset and keep **Timed repeats** at 3. Click **Run measured benchmark**. The browser performs a warm-up and three timed jobs per active-consumer count, then saves the report. Open **Experiments** to show the measured table and chart. State that these are active consumers among already-running containers; the dashboard does not create Docker containers. Discuss the measured trend honestly rather than claiming linear speedup.

### 4:30–6:30 — Worker failure and recovery

In **Demo center**, click **Use/create 500-document test dataset**, then **Start failure-tolerance workload**. While it is processing, click **Kill one active worker (SIMULATED)**. The application deliberately terminates one busy worker process. Wait for the dashboard to report RabbitMQ redelivery, worker recovery, both integrity checks, and baseline verification. If the worker finishes before the kill button becomes available, restart this workload and try again; do not claim a kill demonstration unless redelivery is observed.

### 6:30–7:15 — Retry to terminal failure

Select the 200-document or 10-document corpus and click **Run simulated poison-task job**. Show the three retries, terminal `completed_with_failures` status, failed-task queue evidence, valid pair coverage, and baseline result. Explain that the intentionally invalid task is an extra task and contributes zero document pairs.

### 7:15–8:00 — Wall time versus Lamport time

Select the 10-document corpus, leave the simulated worker offset at `-3600`, and click **Run clock-order demo**. The dashboard opens **Timeline** after completion. Compare the wall-clock and Lamport lists: reported wall time can place a receive event before its send, while Lamport counters preserve send-before-receive causality. Return to **Demo center** and click **Reset offset to zero**.

### 8:00–9:00 — Inter-service communication

Show **Timeline** for the completed job: API task publication, worker receipt/completion, and aggregator result receipt. On **System**, show the separate service health, RabbitMQ consumers and queue counts, Compose DNS addresses, and worker identities. If terminal logs are desired as supplementary evidence, run `docker compose logs --since=5m api aggregator worker`; it is not needed to operate the browser demonstrations.

### 9:00–10:00 — Close with honest limits

State that the project runs on one host, uses a shared Docker volume (not a distributed file system), keeps in-flight aggregator state in memory, and has an aggregator single point of failure. Delivery is at-least-once with result de-duplication. Clock skew and failure injection are simulated controls. Similarity is textual overlap, not a plagiarism verdict. See [`limitations.md`](limitations.md).

## PowerShell fallback

The existing `scripts/demo_*.ps1` helpers remain available if a browser control cannot be used. Run them from the repository root only after confirming Docker Desktop and the API are healthy. The UI is the preferred evaluation path.
